# Protocol Specification

## Packet Structure

```
┌──────────────────────────────────────┐
│ Header (38 bytes)                    │
├──────────────────────────────────────┤
│ Segment 0                            │
│ Segment 1                            │
│ ...                                  │
└──────────────────────────────────────┘
```

Protocol max packet MTU: `protocol_max_packet_mtu` is a buffer-sizing guard
(header + segments), not a transport MTU cap. Transports compute their own
packet MTUs based on encoding overhead and configured caps.
Minimum packet MTU is `PACKET_HEADER_SIZE + SEGMENT_HEADER_SIZE + 1` (segment-
capable). Keepalive-only packets are smaller.
Pre-negotiation packet MTUs come from transport send/recv limits; payload bytes
are limited to (packet_mtu - PACKET_HEADER_SIZE) per direction until MTU_OK.
The header is always added on the wire.

Packet encryption is optional. When enabled, the header is sent in cleartext
and only the body (segments) is encrypted with the PSK. Transport MTUs are
per-direction packet bytes; `tun.mtu` values carry payload bytes only.
RC4 and SHA256 derive per-packet keystreams from (seq, direction); keystreams
repeat if seq wraps under a static PSK.
SHA256 derives a packet key using HMAC-SHA256(psk, 'sfb-sha256' + nonce) and
generates keystream blocks as SHA256(packet_key + counter_be32).

---

## Header (38 bytes)

```
 0       1       2       3
┌───────┬───────┬───────┬───────┐
│      seq      │      ack      │
└───────┴───────┴───────┴───────┘
 4                               35
┌──────────────────────────────────────┐
│           sack (256 bits)            │
└──────────────────────────────────────┘
 36      37
┌───────┬───────┐
│ flags │  rsvd │
└───────┴───────┘
```

| Field    | Size | Description                                 |
|----------|------|---------------------------------------------|
| seq      | 2    | Sequence number of this packet              |
| ack      | 2    | Next expected sequence number from peer     |
| sack     | 32   | Bitmap of 256 packets received beyond ack  |
| flags    | 1    | Packet flags                                |
| rsvd     | 1    | Reserved (must be 0)                        |

All multi-byte fields are big-endian.

Segments follow the header. Parse by reading each segment header (channel + len)
and consuming len bytes of payload, repeating until the packet is exhausted.

Wire-format note: The 256-bit SACK header layout is not backward-compatible.
Both sides must be upgraded together.

---

## Flags

```
Bit 0 (0x01): SYN - Handshake initiation
Bit 1 (0x02): ACK - Handshake acknowledgment
Bit 2 (0x04): KEEPALIVE - Idle keepalive packet (no segments)
Bit 3 (0x08): HAS_SEGMENTS - Packet contains one or more segments
Bit 4 (0x10): Reserved (must be 0)
Bits 5-7:     Reserved (must be 0)
```

SYN and ACK are only used during the handshake. After connection establishment,
every non-handshake packet must set exactly one content flag:
`HAS_SEGMENTS` or `KEEPALIVE`.

Handshake constraints:
- SYN/SYN+ACK/ACK packets MUST contain zero segments
- SYN/SYN+ACK/ACK packets MUST NOT set any content flags
- Once CONNECTED, any packet with SYN or ACK flags is treated as a stale
  handshake packet. Receivers MUST NOT reset connection state on these packets.
  They MAY ignore them for reliability processing; if processed, they are
  treated as ack-only (no segments). For polling transports, responders should
  still send a normal response to satisfy the request/response contract.

Content-flag constraints (post-ACK):
- Exactly one of `HAS_SEGMENTS` or `KEEPALIVE` is set
- `HAS_SEGMENTS` requires at least one segment
- `KEEPALIVE` requires zero segments
- Empty packets (zero segments) MUST set `KEEPALIVE` (ack-only packets are invalid)
- Reserved flag bits (4-7) MUST be 0
- "Real data" for pacing is any packet with `HAS_SEGMENTS` (control or data)
- Any violation is a fatal protocol error (log, drop, close)

---

## SACK Bitmap

The 256-bit SACK field represents packets received beyond the cumulative `ack`.

- Bit 0 = ack + 1 received
- Bit 1 = ack + 2 received
- ...
- Bit 255 = ack + 256 received

The bitmap is encoded as a 32-byte big-endian integer on the wire. The highest
order bit maps to offset 256, and the lowest order bit maps to offset 1.

Example: ack=100, sack=0x0000000000000000000000000000000000000000000000000000000000000014
- Received: 100 and below (cumulative), 102, 104
- Missing: 101, 103

---

## Segment Header (3 bytes)

```
 0       1       2
┌───────┬───────┬───────┐
│channel│     len       │
└───────┴───────┴───────┘
```

| Field   | Size | Description                        |
|---------|------|------------------------------------|
| channel | 1    | Channel ID                         |
| len     | 2    | Payload length (following header)  |

Payload immediately follows header.

---

## Channel IDs

- Channel 0: Control channel (always open, reserved)
- Odd channels (1, 3, 5...): Dynamically opened by Alice
- Even channels (2, 4, 6...): Dynamically opened by Bob

The even/odd convention applies only to dynamically opened channels and prevents
ID collisions when both sides open channels concurrently.

### Channel 0 Protocol Errors

Channel 0 is reserved for control messages and is always open. Any channel
lifecycle control message that references channel 0 is a fatal protocol error,
including: `open`, `open_ok`, `open_fail`, `close`, `close_ok`, `close_err`,
and `half_close`.

On violation:
- Log a protocol error with the offending message.
- Drop the offending message.
- Close the tunnel immediately.
Enforce this during tunnel control dispatch before channel manager handling.

---

## Control Messages (Channel 0)

Control messages are JSON objects sent on channel 0. See `doc/architecture/CONTROL_MESSAGES.md`
for the complete control message specification including:

- Message format (`t` type field, `c` command field)
- Type registry (tunnel, channel, module types)
- Dispatch rules
- Full message definitions

### Quick Reference

All messages use the format `{"t":"<type>","c":"<command>",...}`:

```json
{"t":"tun","c":"mtu","tx":500,"rx":150}
{"t":"ch","c":"open","ch":2,"atype":"ipv4","addr":"192.168.1.1","port":8080}
{"t":"ch","c":"half_close","ch":2}
{"t":"ch","c":"close","ch":2}
{"t":"ch","c":"close_err","ch":2,"code":"aborted","reason":"Channel aborted"}
```

Keepalive is a header flag with zero segments, not a channel 0 message.

Address types labeled `ipv4` are the only supported address type in the
protocol; IPv6 is unsupported.

Module control messages (all `t` values except `tun` and `ch`) must include
`mid` as a positive integer module instance id. The default instance id is `1`.

### Message Types

| Type | Description |
|------|-------------|
| `tun` | Tunnel: mtu/window negotiation (keepalive is a header flag) |
| `ch` | Channel: open/close lifecycle |
| `mod` | Module loader: load/unload module instances |
| `file` | File transfer module |
| `sock` | SOCKS proxy module |
| `sh` | Shell module |

### Channel Half-Close

`{"t":"ch","c":"half_close","ch":<id>}` indicates the sender will not send
more data on the channel. The receiver marks the receive side closed and
returns `b''` on read once its buffer drains. The channel remains open for
sending until closed with `close`/`close_ok`. A half-close targeting channel 0
is a fatal protocol error.

Half-close is required for correct stream semantics; mixed versions are
unsupported.

### MTU Negotiation

Immediately after handshake, Alice and Bob negotiate payload size for the
`tun.mtu` control message:

1. Alice sends: `{"t":"tun","c":"mtu","tx":X,"rx":Y}`
2. Bob responds: `{"t":"tun","c":"mtu_ok","tx":Yb,"rx":Xb}` where:
   - Xb = min(X, bob_recv_max)
   - Yb = min(Y, bob_send_max)

The tun_mtu fields carry payload bytes and require both `tx` and `rx`. The
on-wire packet MTU for each direction is payload bytes + PACKET_HEADER_SIZE.
The bob_recv_max and bob_send_max values are derived from transport
recv_packet_mtu/send_packet_mtu by subtracting PACKET_HEADER_SIZE.
`tx`/`rx` must be integers >= (SEGMENT_HEADER_SIZE + 1); missing/invalid values
are protocol violations (log, close).
An `mtu_ack` is only valid when the receiver has a pending send MTU increase.

Until `mtu_ok` is received, both sides limit packets to their transport-derived
send/recv packet MTUs and payload bytes to those minus PACKET_HEADER_SIZE.

### Window Negotiation

After MTU negotiation, Alice and Bob negotiate the send window:

1. Alice sends: `{"t":"tun","c":"window","size":X}`
2. Bob responds: `{"t":"tun","c":"window_ok","size":Y}` where Y = min(X, bob_max, 256)

Maximum is 256 (SACK bitmap size). Until `window_ok` is received, use max_in_flight = 1.

---

## Handshake

1. Alice sends: SYN flag, seq=1, no segments
2. Bob sends: SYN+ACK flags, seq=1, ack=2, no segments
3. Alice sends: ACK flag, seq=2, ack=2, no segments

Initial sequence number (ISN) is fixed at 1 for both sides.

Connection established. Both sides begin normal operation. Only one connection
is active at a time; Bob ignores any traffic he does not understand.

For polling transports, the handshake completes in 2 round-trips:
- Round 1: Alice sends SYN (query), Bob responds SYN+ACK (response)
- Round 2: Alice sends ACK (query), Bob responds with `HAS_SEGMENTS`,
  or `KEEPALIVE` (no SYN/ACK flags)

---

## MTU Negotiation

Both sides start with transport-derived send/recv packet MTUs. Immediately
after handshake, Alice and Bob negotiate a larger MTU:

1. Alice sends: `{"t":"tun","c":"mtu","tx":X,"rx":Y}` where X is her send max and Y is her recv max
2. Bob responds: `{"t":"tun","c":"mtu_ok","tx":Yb,"rx":Xb}` where:
   - Xb = min(X, bob_recv_max)
   - Yb = min(Y, bob_send_max)
3. Both sides now use the negotiated per-direction MTUs

```
Alice                              Bob
  │                                  │
  │←───────── (handshake) ──────────→│
  │                                  │
  │── {t:tun,c:mtu,tx:500,rx:150} ──▶│  Alice proposes tx=500, rx=150 (payload)
  │◀── {t:tun,c:mtu_ok,tx:150,rx:500}│  Bob clamps each direction (payload)
  │                                  │
  │     MTUs are now tx=150, rx=500  │  (payload)
```

Until MTU_OK is received, both sides must limit payloads to their
transport-derived payload caps (packet MTU minus PACKET_HEADER_SIZE). The MTU
control messages themselves are well under this limit (header added on the wire).

The negotiated MTU applies to payload bytes (segments only). The on-wire packet
MTU is payload bytes + PACKET_HEADER_SIZE.
Transport send_packet_mtu/recv_packet_mtu are packet bytes; BaseTunnel converts
them to payload bytes by subtracting PACKET_HEADER_SIZE for `tun.mtu`.
Each transport computes its max based on encoding overhead (e.g., base32 for
DNS queries, base64 for DNS responses).

---

## Window Negotiation

Immediately after MTU negotiation, Alice and Bob negotiate the send window:

1. Alice sends: `{"t":"tun","c":"window","size":X}` where X is her preferred max_in_flight
2. Bob responds: `{"t":"tun","c":"window_ok","size":Y}` where Y = min(X, bob_max, 256)
3. Both sides now use Y as max_in_flight

```
Alice                              Bob
  │                                  │
  │←───────── (handshake) ──────────→│
  │←──────── (MTU negotiation) ─────→│
  │                                  │
  │── {t:tun,c:window,size:256} ─────▶│  Alice proposes 256
  │◀── {t:tun,c:window_ok,size:32} ──│  Bob's max is 32, use 32
  │                                  │
  │      max_in_flight is now 32     │
```

The maximum value is capped at 256 to match the SACK bitmap size. This guarantees:

- The sender cannot have more packets in-flight than the SACK can represent
- The receiver's out-of-order buffer never exceeds 256 packets
- All gaps within the window are visible to the sender via SACK

Until WINDOW_OK is received, both sides use max_in_flight = 1 (stop-and-wait).

---

## Reliability

- Each packet sent increments sender's seq by 1
- Receiver sends ack = highest contiguous seq received + 1
- Receiver sets sack bitmap for out-of-order packets beyond ack
- Sender uses sack to skip retransmitting selectively-acked packets
- Decode errors are logged and dropped. Invalid content-flag packets are
  treated as protocol violations and close the tunnel.

### Retransmission (Asymmetric)

Both sides can initiate tunnel-level operations, but Bob cannot act on timers
because he can only transmit when Alice polls. This affects retransmission:

**Alice:**
- Tracks RTT and computes retransmit timeout (RTO)
- Retransmits unacked packets when RTO expires
- Retransmits reuse an in-flight sequence number and do not create new
  outstanding slots
- Timer-driven: can decide *when* to retransmit
 - Retransmits carry current ack/sack state in the packet header

**Bob:**
- Cannot act on timers; can only transmit in response to polls
- On each poll: if unacked packets exist and cooldown allows, retransmit the
  oldest unacked packet (skips after recent ack progress)
- Retransmits reuse an in-flight sequence number and do not create new
  outstanding slots
- Opportunity-driven: retransmits when polled, not when a timer fires
- Does not track RTT (Alice's polling interval dominates, not network latency)
 - Retransmits carry current ack/sack state in the packet header

### Sequence Number Wraparound

Sequence numbers are 16-bit and wrap from 65535 to 0. Implementations must use
modular arithmetic for all seq/ack comparisons:

```
def seq_lt(a, b):
    """Return True if a < b in sequence space (handles wraparound)."""
    return ((b - a) & 0xFFFF) < 0x8000 and a != b
```

A packet is considered "newer" if it is within 32767 of the current position
in the forward direction.

### RTT Estimation (Alice only)

Alice tracks round-trip time to compute her retransmit timeout:

```
srtt = 0.875 * srtt + 0.125 * sample
rto = srtt * 2
rto = clamp(rto, 500ms, 10s)
```

**Initialization:** Before the first RTT sample, use rto = 1000ms (1 second).
After the first sample, set srtt = sample (no smoothing on first measurement).
Defaults are configurable via protocol_initial_rto_ms, protocol_min_rto_ms,
and protocol_max_rto_ms (1000/500/10000ms).

**Karn's Algorithm:** Do not use RTT samples from retransmitted packets. When
a packet is retransmitted and later acknowledged, it is ambiguous whether the
ack is for the original or the retransmit. Using such samples can corrupt the
RTT estimate. Only sample RTT from packets acknowledged on their first
transmission.

RTT samples are only taken when the response carries `HAS_SEGMENTS`.
`KEEPALIVE` responses do not produce RTT samples or reset backoff. When
sampling is enabled (`HAS_SEGMENTS` response), RTT samples are taken from newly
acked first-transmission packets; `KEEPALIVE` packets are excluded.

**RTO Backoff:** On each consecutive retransmit of the same packet without
receiving an ack, double the RTO (exponential backoff) up to the 10s maximum.
Reset to the computed value after receiving a valid RTT sample.

Bob does not track RTT; his "round-trip" is dominated by Alice's polling
interval rather than network latency.

### SACK Coverage Guarantee

The 256-bit SACK bitmap represents packets ack+1 through ack+256. Because
max_in_flight is capped at 256, the SACK bitmap always covers the entire
send window:

- The sender cannot have more than 256 packets in-flight
- All in-flight packets fall within SACK's representable range
- The sender always has complete visibility into which packets were received

This eliminates the "blind retransmission" scenario where the sender must
guess which packets to retransmit. Every gap is visible via SACK.

---

## Windowing

### Send Window

- Both sides use the negotiated max_in_flight (see Window Negotiation)
- Maximum value is 256 (SACK bitmap size); minimum is 1
- Sender can have up to max_in_flight unacked packets outstanding
- Window slides forward as cumulative acks are received
- Retransmits reuse an existing sequence number and do not add to the
  outstanding count

The send window tracks reliability (how many unacked packets are outstanding).
Transport pipelining uses the same cap as the negotiated window
(`max_in_flight`), so there is no separate transport-specific limit.
The transport's `send()`/`recv()` interface allows multiple outstanding
requests; responses may arrive out of order. The reliability layer handles
reordering via sequence numbers and SACK.

### Flow Control (Natural Throttling)

There is no explicit receive window advertisement. Flow control emerges from
the protocol's asymmetric nature:

**Bob is naturally throttled by Alice's query rate.** Bob can only send one
packet per query he receives. If Alice slows her polling, Bob's throughput
decreases automatically. Alice controls the overall data rate.

**Alice is throttled by max_in_flight.** Alice cannot have more than
max_in_flight packets outstanding. If she sends faster than Bob can ack,
she blocks until acks arrive. This bounds how much data can be in-flight.

**If either side's processing slows:**
- Acks are delayed (they're piggybacked on outgoing packets)
- The sender's window fills up (max_in_flight unacked packets)
- The sender naturally pauses until acks arrive

This self-regulating behavior eliminates the need for explicit receive window
signaling.

---

## Connection Timeout (Asymmetric)

Bob can only transmit in response to polls, so timeout detection differs:

**Alice:**
- Timeout after 30 consecutive packets sent without receiving any response
- "Response" means any packet from Bob (which carries an ack field), not
  specifically cumulative ack advancement
- This detects a dead connection (Bob stopped responding entirely)
- Stuck cumulative ack with active responses indicates packet loss, which
  retransmission handles—not a timeout condition
- Packet-based rather than wall-clock to accommodate variable polling intervals

**Bob:**
- Timeout after 60 seconds with no poll received from Alice
- Wall-clock based: if Alice stops polling, Bob has no packets to count
- Detects dead connection via silence rather than failed sends
