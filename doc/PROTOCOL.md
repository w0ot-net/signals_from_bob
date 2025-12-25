# Protocol Specification

## Packet Structure

```
┌──────────────────────────────────────┐
│ Header (8 bytes)                     │
├──────────────────────────────────────┤
│ Segment 0                            │
│ Segment 1                            │
│ ...                                  │
└──────────────────────────────────────┘
```

Protocol max packet size: 1450 bytes (configurable per transport)
Pre-negotiation packet size limit is 100 bytes for all transports until MTU_OK.

Packet encryption is optional. When enabled, the entire packet is encrypted
with PSK before transmission. Transports may impose a smaller MTU than the
protocol max packet size.

---

## Header (8 bytes)

```
 0       1       2       3       4       5       6       7
┌───────┬───────┬───────┬───────┬───────┬───────┬───────┬───────┐
│      seq      │      ack      │     sack      │ flags │  rsvd │
└───────┴───────┴───────┴───────┴───────┴───────┴───────┴───────┘
```

| Field    | Size | Description                                 |
|----------|------|---------------------------------------------|
| seq      | 2    | Sequence number of this packet              |
| ack      | 2    | Next expected sequence number from peer     |
| sack     | 2    | Bitmap of 16 packets received beyond ack    |
| flags    | 1    | Packet flags                                |
| rsvd     | 1    | Reserved (must be 0)                        |

All multi-byte fields are big-endian.

Segments follow the header. Parse by reading each segment header (channel + len)
and consuming len bytes of payload, repeating until the packet is exhausted.

---

## Flags

```
Bit 0 (0x01): SYN - Handshake initiation
Bit 1 (0x02): ACK - Handshake acknowledgment
Bits 2-7:     Reserved (must be 0)
```

SYN and ACK are only used during the handshake. After connection establishment,
flags is 0 for all data packets.

---

## SACK Bitmap

The 16-bit SACK field represents packets received beyond the cumulative `ack`.

- Bit 0 = ack + 1 received
- Bit 1 = ack + 2 received
- ...
- Bit 15 = ack + 16 received

Example: ack=100, sack=0b0000000000010100
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

---

## Control Messages (Channel 0)

Control messages are JSON objects sent on channel 0. See `doc/CONTROL_MESSAGES.md`
for the complete control message specification including:

- Message format (`t` type field, `c` command field)
- Type registry (tunnel, channel, module types)
- Dispatch rules
- Full message definitions

### Quick Reference

All messages use the format `{"t":"<type>","c":"<command>",...}`:

```json
{"t":"tun","c":"ping"}
{"t":"tun","c":"mtu","size":500}
{"t":"ch","c":"open","ch":2,"atype":"ipv4","addr":"192.168.1.1","port":8080}
{"t":"ch","c":"close","ch":2}
```

### Message Types

| Type | Description |
|------|-------------|
| `tun` | Tunnel: ping/pong, mtu, window negotiation |
| `ch` | Channel: open/close lifecycle |
| `file` | File transfer module |
| `sock` | SOCKS proxy module |
| `sh` | Shell module |

### MTU Negotiation

Immediately after handshake, Alice and Bob negotiate packet size:

1. Alice sends: `{"t":"tun","c":"mtu","size":X}`
2. Bob responds: `{"t":"tun","c":"mtu_ok","size":Y}` where Y = min(X, bob_max)

Until `mtu_ok` is received, both sides limit packets to 100 bytes.

### Window Negotiation

After MTU negotiation, Alice and Bob negotiate the send window:

1. Alice sends: `{"t":"tun","c":"window","size":X}`
2. Bob responds: `{"t":"tun","c":"window_ok","size":Y}` where Y = min(X, bob_max, 16)

Maximum is 16 (SACK bitmap size). Until `window_ok` is received, use max_in_flight = 1.

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
- Round 2: Alice sends ACK (query), Bob responds with data or PONG (response)

---

## MTU Negotiation

Both sides start with a default MTU of 100 bytes. Immediately after handshake,
Alice and Bob negotiate a larger MTU:

1. Alice sends: `{"t":"tun","c":"mtu","size":X}` where X is her transport's max
2. Bob responds: `{"t":"tun","c":"mtu_ok","size":Y}` where Y = min(X, bob_max)
3. Both sides now use Y as the packet size limit

```
Alice                              Bob
  │                                  │
  │←───────── (handshake) ──────────→│
  │                                  │
  │── {t:tun,c:mtu,size:500} ────────▶│  Alice proposes 500
  │◀── {t:tun,c:mtu_ok,size:150} ────│  Bob's max is 150, use 150
  │                                  │
  │         MTU is now 150           │
```

Until MTU_OK is received, both sides must limit packets to 100 bytes. The MTU
control messages themselves are well under this limit.

The negotiated MTU applies to the entire packet (header + segments), whether
encrypted or not.
Each transport computes its max based on encoding overhead (e.g., base32 for
DNS queries, base64 for DNS responses).

---

## Window Negotiation

Immediately after MTU negotiation, Alice and Bob negotiate the send window:

1. Alice sends: `{"t":"tun","c":"window","size":X}` where X is her preferred max_in_flight
2. Bob responds: `{"t":"tun","c":"window_ok","size":Y}` where Y = min(X, bob_max, 16)
3. Both sides now use Y as max_in_flight

```
Alice                              Bob
  │                                  │
  │←───────── (handshake) ──────────→│
  │←──────── (MTU negotiation) ─────→│
  │                                  │
  │── {t:tun,c:window,size:16} ──────▶│  Alice proposes 16
  │◀── {t:tun,c:window_ok,size:8} ───│  Bob's max is 8, use 8
  │                                  │
  │      max_in_flight is now 8      │
```

The maximum value is capped at 16 to match the SACK bitmap size. This guarantees:

- The sender cannot have more packets in-flight than the SACK can represent
- The receiver's out-of-order buffer never exceeds 16 packets
- All gaps within the window are visible to the sender via SACK

Until WINDOW_OK is received, both sides use max_in_flight = 1 (stop-and-wait).

---

## Reliability

- Each packet sent increments sender's seq by 1
- Receiver sends ack = highest contiguous seq received + 1
- Receiver sets sack bitmap for out-of-order packets beyond ack
- Sender uses sack to skip retransmitting selectively-acked packets
- Malformed packets are silently dropped; reliability handles retransmission

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
- On each poll: if unacked packets exist, retransmit oldest unacked packet
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

**Karn's Algorithm:** Do not use RTT samples from retransmitted packets. When
a packet is retransmitted and later acknowledged, it is ambiguous whether the
ack is for the original or the retransmit. Using such samples can corrupt the
RTT estimate. Only sample RTT from packets acknowledged on their first
transmission.

**RTO Backoff:** On each consecutive retransmit of the same packet without
receiving an ack, double the RTO (exponential backoff) up to the 10s maximum.
Reset to the computed value after receiving a valid RTT sample.

Bob does not track RTT; his "round-trip" is dominated by Alice's polling
interval rather than network latency.

### SACK Coverage Guarantee

The 16-bit SACK bitmap represents packets ack+1 through ack+16. Because
max_in_flight is capped at 16, the SACK bitmap always covers the entire
send window:

- The sender cannot have more than 16 packets in-flight
- All in-flight packets fall within SACK's representable range
- The sender always has complete visibility into which packets were received

This eliminates the "blind retransmission" scenario where the sender must
guess which packets to retransmit. Every gap is visible via SACK.

---

## Windowing

### Send Window

- Both sides use the negotiated max_in_flight (see Window Negotiation)
- Maximum value is 16 (SACK bitmap size); minimum is 1
- Sender can have up to max_in_flight unacked packets outstanding
- Window slides forward as cumulative acks are received
- Retransmits reuse an existing sequence number and do not add to the
  outstanding count

The send window tracks reliability (how many unacked packets are outstanding).
Transport pipelining is a separate but related limit:

```
effective_in_flight = min(
    tunnel.negotiated_window,   # Reliability limit (max 16)
    transport.max_pending,      # Transport limit
)
```

Alice can have up to `effective_in_flight` packets in flight simultaneously.
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
